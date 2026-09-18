"""Order lifecycle, state machine, idempotency and crash recovery."""
from __future__ import annotations
import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set
from loguru import logger
import sys
_ai_root = Path(__file__).resolve().parent.parent
if str(_ai_root) not in sys.path:
    sys.path.insert(0, str(_ai_root))
from execution.exchange_adapter import ExchangeAdapter, ExchangeOrder, OrderType, OrderSide, TimeInForce, PositionSide, ExchangeError, RateLimitError

class OrderState(str, Enum):
    NEW="NEW"; SUBMITTED="SUBMITTED"; ACCEPTED="ACCEPTED"; PARTIALLY_FILLED="PARTIALLY_FILLED"; FILLED="FILLED"; CANCELLED="CANCELLED"; REJECTED="REJECTED"; EXPIRED="EXPIRED"; FAILED="FAILED"; UNKNOWN="UNKNOWN"
class OrderPurpose(str, Enum):
    ENTRY="ENTRY"; STOP_LOSS="STOP_LOSS"; TAKE_PROFIT="TAKE_PROFIT"; TRAILING_STOP="TRAILING_STOP"; REDUCE="REDUCE"; CLOSE="CLOSE"

@dataclass
class OrderRecord:
    order_id:str=""; client_order_id:str=""; exchange_order_id:int=0; signal_id:str=""
    symbol:str=""; side:str=""; order_type:str=""; purpose:str=""; quantity:float=0.0; price:float=0.0; stop_price:float=0.0
    time_in_force:str="GTC"; reduce_only:bool=False; close_position:bool=False; leverage:int=1; position_side:str="BOTH"
    state:str=OrderState.NEW.value; executed_qty:float=0.0; avg_price:float=0.0; cum_quote:float=0.0; fees:float=0.0
    created_at:float=0.0; submitted_at:float=0.0; accepted_at:float=0.0; filled_at:float=0.0; cancelled_at:float=0.0; updated_at:float=0.0
    attempt:int=0; rejection_reason:str=""; failure_reason:str=""; timeout_sec:float=0.0; idempotency_key:str=""; state_history:List[Dict]=field(default_factory=list)
    def __post_init__(self):
        if not self.created_at: self.created_at=time.time()
        if not self.updated_at: self.updated_at=self.created_at
        if not self.idempotency_key: self.idempotency_key=f"{self.signal_id}:{self.purpose}:{self.symbol}"
    def to_dict(self)->Dict: return asdict(self)
    def transition(self,new_state:str,reason:str="")->None:
        old_state=self.state; self.state=new_state; self.updated_at=time.time()
        self.state_history.append({"from":old_state,"to":new_state,"reason":reason,"time":self.updated_at})
        if new_state==OrderState.SUBMITTED.value: self.submitted_at=self.updated_at
        elif new_state==OrderState.ACCEPTED.value: self.accepted_at=self.updated_at
        elif new_state==OrderState.FILLED.value: self.filled_at=self.updated_at
        elif new_state==OrderState.CANCELLED.value: self.cancelled_at=self.updated_at

class OrderManager:
    STATE_FILE=_ai_root/"data"/"execution"/"order_state.json"; MAX_ORDER_AGE_SEC=3600; ORDER_TIMEOUT_SEC=30
    def __init__(self,exchange:ExchangeAdapter)->None:
        self._exchange=exchange; self._orders={}; self._by_exchange={}; self._by_signal={}; self._idempotency=set(); self._idempotency_map={}; self._lock=asyncio.Lock()
        self._on_fill_callback=None; self._on_cancel_callback=None; self._on_reject_callback=None; self.STATE_FILE.parent.mkdir(parents=True,exist_ok=True)
    def set_callbacks(self,on_fill=None,on_cancel=None,on_reject=None): self._on_fill_callback=on_fill; self._on_cancel_callback=on_cancel; self._on_reject_callback=on_reject
    async def create_order(self,signal_id,symbol,side,order_type,purpose,quantity,price=0.0,stop_price=0.0,time_in_force=TimeInForce.GTC,reduce_only=False,close_position=False,leverage=1,position_side=PositionSide.BOTH,timeout_sec=0.0):
        idempotency_key=f"{signal_id}:{purpose.value}:{symbol}"
        async with self._lock:
            if idempotency_key in self._idempotency:
                existing_id=self._idempotency_map.get(idempotency_key)
                if existing_id and existing_id in self._orders:
                    existing=self._orders[existing_id]
                    if existing.state not in (OrderState.CANCELLED.value,OrderState.REJECTED.value,OrderState.FILLED.value,OrderState.FAILED.value,OrderState.EXPIRED.value):
                        return None
                    self._idempotency.discard(idempotency_key); self._idempotency_map.pop(idempotency_key,None)
                else: self._idempotency.discard(idempotency_key)
            order_id=str(uuid.uuid4()); client_order_id=f"DT-{order_id[:12]}-{int(time.time())}"
            order=OrderRecord(order_id=order_id,client_order_id=client_order_id,signal_id=signal_id,symbol=symbol,side=side,order_type=order_type.value,purpose=purpose.value,quantity=quantity,price=price,stop_price=stop_price,time_in_force=time_in_force.value,reduce_only=reduce_only,close_position=close_position,leverage=leverage,position_side=position_side.value,timeout_sec=timeout_sec or self.ORDER_TIMEOUT_SEC,idempotency_key=idempotency_key)
            self._orders[order_id]=order; self._idempotency.add(idempotency_key); self._idempotency_map[idempotency_key]=order_id; self._by_signal.setdefault(signal_id,[]).append(order_id)
        await self._submit_order(order); return order

    def _apply_exchange(self,order:OrderRecord,exchange_order:ExchangeOrder)->None:
        order.exchange_order_id=exchange_order.order_id
        self._by_exchange[exchange_order.order_id]=order.order_id
        order.executed_qty=exchange_order.executed_qty; order.avg_price=exchange_order.avg_price; order.cum_quote=exchange_order.cum_quote

    async def _submit_order(self,order:OrderRecord)->None:
        try:
            order.transition(OrderState.SUBMITTED.value,"Sent to exchange"); order.attempt+=1
            eo=await self._exchange.place_order(symbol=order.symbol,side=OrderSide(order.side),order_type=OrderType(order.order_type),quantity=order.quantity,price=order.price,stop_price=order.stop_price,time_in_force=TimeInForce(order.time_in_force),reduce_only=order.reduce_only,close_position=order.close_position,position_side=PositionSide(order.position_side),client_order_id=order.client_order_id)
            self._apply_exchange(order,eo); status=eo.status
            if status=="NEW": order.transition(OrderState.ACCEPTED.value,"Accepted by exchange")
            elif status=="PARTIALLY_FILLED": order.transition(OrderState.PARTIALLY_FILLED.value,"Partial fill")
            elif status=="FILLED":
                order.transition(OrderState.FILLED.value,"Fully filled")
                if self._on_fill_callback: await self._on_fill_callback(order)
            elif status=="CANCELED": order.transition(OrderState.CANCELLED.value,"Cancelled by exchange")
            elif status=="REJECTED":
                order.rejection_reason=f"Exchange rejected: {status}"; order.transition(OrderState.REJECTED.value,order.rejection_reason)
                if self._on_reject_callback: await self._on_reject_callback(order)
            elif status=="EXPIRED": order.transition(OrderState.EXPIRED.value,"Order expired")
        except (RateLimitError,ExchangeError) as exc:
            # Any failure after a POST is potentially ambiguous. Reconcile by clientOrderId before terminal failure.
            if order.state==OrderState.SUBMITTED.value and not order.exchange_order_id:
                try:
                    eo=await self._exchange.get_order(symbol=order.symbol,client_order_id=order.client_order_id)
                    self._apply_exchange(order,eo)
                    if eo.status=="FILLED":
                        order.transition(OrderState.FILLED.value,"Reconciled after ambiguous submission")
                        if self._on_fill_callback: await self._on_fill_callback(order)
                        return
                    if eo.status=="PARTIALLY_FILLED": order.transition(OrderState.PARTIALLY_FILLED.value,"Reconciled partial fill"); return
                    if eo.status=="NEW": order.transition(OrderState.ACCEPTED.value,"Reconciled accepted order"); return
                    if eo.status=="CANCELED": order.transition(OrderState.CANCELLED.value,"Reconciled cancellation"); return
                    if eo.status=="REJECTED": order.rejection_reason="Reconciled rejection"; order.transition(OrderState.REJECTED.value,order.rejection_reason); return
                    if eo.status=="EXPIRED": order.transition(OrderState.EXPIRED.value,"Reconciled expiry"); return
                except Exception as rec_exc:
                    lower=str(rec_exc).lower()
                    if "order does not exist" not in lower and "-2013" not in lower:
                        order.failure_reason=f"Submission outcome unknown; reconciliation unavailable: {rec_exc}"; order.transition(OrderState.UNKNOWN.value,order.failure_reason); return
                    # Order genuinely not found: terminal failure is safe only after exchange lookup by client id.
            if isinstance(exc,RateLimitError): order.failure_reason=f"Rate limited: {exc}"
            else: order.failure_reason=str(exc)
            order.transition(OrderState.FAILED.value,order.failure_reason)
        except Exception as exc:
            order.failure_reason=str(exc); order.transition(OrderState.FAILED.value,str(exc))

    async def sync_order(self,order_id:str)->Optional[OrderRecord]:
        order=self._orders.get(order_id)
        if not order:return order
        try:
            eo=await self._exchange.get_order(symbol=order.symbol,order_id=order.exchange_order_id) if order.exchange_order_id else await self._exchange.get_order(symbol=order.symbol,client_order_id=order.client_order_id) if order.state==OrderState.UNKNOWN.value else None
            if eo is None:return order
            old_state=order.state; self._apply_exchange(order,eo); status=eo.status
            if status=="NEW" and old_state in (OrderState.UNKNOWN.value,OrderState.SUBMITTED.value): order.transition(OrderState.ACCEPTED.value,"Synced: accepted")
            elif status=="FILLED" and old_state!=OrderState.FILLED.value:
                order.transition(OrderState.FILLED.value,"Synced: filled")
                if self._on_fill_callback: await self._on_fill_callback(order)
            elif status=="CANCELED" and old_state!=OrderState.CANCELLED.value: order.transition(OrderState.CANCELLED.value,"Synced: cancelled")
            elif status=="REJECTED" and old_state!=OrderState.REJECTED.value:
                order.rejection_reason="Synced: rejected"; order.transition(OrderState.REJECTED.value,order.rejection_reason)
                if self._on_reject_callback: await self._on_reject_callback(order)
            elif status=="EXPIRED" and old_state!=OrderState.EXPIRED.value: order.transition(OrderState.EXPIRED.value,"Synced: expired")
            elif status=="PARTIALLY_FILLED": order.transition(OrderState.PARTIALLY_FILLED.value,"Synced: partial")
            return order
        except Exception as exc: logger.error("Order sync failed {}: {}",order_id[:8],exc); return order

    async def sync_all_active(self)->int:
        active=[oid for oid,o in self._orders.items() if o.state not in (OrderState.FILLED.value,OrderState.CANCELLED.value,OrderState.REJECTED.value,OrderState.EXPIRED.value,OrderState.FAILED.value)]
        synced=0
        for oid in active:
            if await self.sync_order(oid): synced+=1
        return synced

    async def cancel_order(self,order_id:str,reason:str="")->bool:
        order=self._orders.get(order_id)
        if not order:return False
        if order.state in (OrderState.FILLED.value,OrderState.CANCELLED.value,OrderState.REJECTED.value,OrderState.EXPIRED.value,OrderState.FAILED.value): return False
        if order.state==OrderState.UNKNOWN.value and not order.exchange_order_id:
            # Never mark an exchange-unknown order locally cancelled. Reconcile first.
            await self.sync_order(order_id)
            if order.state==OrderState.UNKNOWN.value and not order.exchange_order_id:
                logger.critical("Refusing local cancellation of unresolved UNKNOWN order {}",order_id[:8]); return False
        try:
            if order.exchange_order_id:
                await self._exchange.cancel_order(symbol=order.symbol,order_id=order.exchange_order_id)
            else:return False
            order.transition(OrderState.CANCELLED.value,reason or "Cancelled by system")
            self._idempotency.discard(order.idempotency_key); self._idempotency_map.pop(order.idempotency_key,None)
            if self._on_cancel_callback: await self._on_cancel_callback(order)
            return True
        except Exception as exc: logger.error("Cancel order failed {}: {}",order_id[:8],exc); return False

    async def cancel_signal_orders(self,signal_id,reason="")->int:
        n=0
        for oid in self._by_signal.get(signal_id,[]): n+=int(await self.cancel_order(oid,reason))
        return n
    async def cancel_symbol_orders(self,symbol,reason="")->int:
        n=0
        for o in list(self._orders.values()):
            if o.symbol==symbol and o.state not in (OrderState.FILLED.value,OrderState.CANCELLED.value,OrderState.REJECTED.value,OrderState.EXPIRED.value,OrderState.FAILED.value): n+=int(await self.cancel_order(o.order_id,reason))
        return n
    async def check_timeouts(self)->int:
        now=time.time(); n=0
        for o in list(self._orders.values()):
            if o.state in (OrderState.SUBMITTED.value,OrderState.ACCEPTED.value,OrderState.PARTIALLY_FILLED.value) and now-o.created_at>o.timeout_sec>0:
                if await self.cancel_order(o.order_id,f"Timeout after {now-o.created_at:.0f}s"): n+=1
        return n
    def get_order(self,order_id): return self._orders.get(order_id)
    def get_order_by_exchange_id(self,exchange_order_id): return self._orders.get(self._by_exchange.get(exchange_order_id)) if exchange_order_id in self._by_exchange else None
    def get_signal_orders(self,signal_id): return [self._orders[x] for x in self._by_signal.get(signal_id,[]) if x in self._orders]
    def get_active_orders(self): return [o for o in self._orders.values() if o.state in (OrderState.NEW.value,OrderState.SUBMITTED.value,OrderState.ACCEPTED.value,OrderState.PARTIALLY_FILLED.value,OrderState.UNKNOWN.value)]
    def get_symbol_orders(self,symbol): return [o for o in self._orders.values() if o.symbol==symbol]
    def _has_purpose(self,signal_id,purpose): return any(o.purpose==purpose and o.state not in (OrderState.CANCELLED.value,OrderState.REJECTED.value,OrderState.FAILED.value,OrderState.EXPIRED.value) for o in self.get_signal_orders(signal_id))
    def has_entry_order(self,signal_id): return self._has_purpose(signal_id,OrderPurpose.ENTRY.value)
    def has_stop_order(self,signal_id): return self._has_purpose(signal_id,OrderPurpose.STOP_LOSS.value)
    def has_tp_order(self,signal_id): return self._has_purpose(signal_id,OrderPurpose.TAKE_PROFIT.value)
    async def save_state(self):
        state={"orders":{oid:o.to_dict() for oid,o in self._orders.items()},"idempotency":list(self._idempotency),"idempotency_map":self._idempotency_map,"saved_at":time.time()}
        tmp=str(self.STATE_FILE)+".tmp"
        with open(tmp,"w") as f: json.dump(state,f,indent=2)
        Path(tmp).rename(self.STATE_FILE)
    async def load_state(self)->int:
        if not self.STATE_FILE.exists():return 0
        try:
            with open(self.STATE_FILE) as f: state=json.load(f)
            for oid,data in state.get("orders",{}).items():
                o=OrderRecord(**data); self._orders[oid]=o
                if o.exchange_order_id:self._by_exchange[o.exchange_order_id]=oid
                self._by_signal.setdefault(o.signal_id,[]).append(oid)
            self._idempotency=set(state.get("idempotency",[])); self._idempotency_map=state.get("idempotency_map",{})
            return len(self._orders)
        except Exception as exc: logger.error("Failed to load order state: {}",exc); return 0
    def get_stats(self):
        states={}
        for o in self._orders.values():states[o.state]=states.get(o.state,0)+1
        return {"total_orders":len(self._orders),"active_orders":len(self.get_active_orders()),"states":states,"idempotency_keys":len(self._idempotency),"signal_count":len(self._by_signal)}
