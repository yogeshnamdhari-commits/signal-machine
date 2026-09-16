import express, { Request, Response } from 'express';
import cors from 'cors';
import helmet from 'helmet';
import morgan from 'morgan';
import { createServer } from 'http';
import { Server as SocketIOServer } from 'socket.io';

import { config } from './config';
import { logger } from './utils/logger';
import { errorHandler } from './middleware/errorHandler';
import { rateLimiter } from './middleware/rateLimiter';
import routes from './routes';
import { websocketService, MarketData } from './services/websocket';
import { signalEngine } from './services/signalEngine';
import { riskManager } from './services/riskManager';
import { marketScanner } from './services/marketScanner';
import { tradeSimulator } from './services/tradeSimulator';

const CANONICAL_SIGNAL_AUTHORITY = process.env.CANONICAL_SIGNAL_AUTHORITY ?? 'python';
if (CANONICAL_SIGNAL_AUTHORITY !== 'python') {
  throw new Error('Unsafe configuration: Python must be the canonical signal authority');
}

// Create Express app
const app = express();
const httpServer = createServer(app);

const io = new SocketIOServer(httpServer, {
  cors: {
    origin: ['http://localhost:5173', 'http://localhost:3000'],
    methods: ['GET', 'POST'],
    credentials: true,
  },
  pingTimeout: config.websocket.pingTimeout,
  pingInterval: config.websocket.pingInterval,
});

app.use(helmet());
app.use(cors({
  origin: ['http://localhost:5173', 'http://localhost:3000'],
  credentials: true,
}));
app.use(morgan('combined', {
  stream: {
    write: (message: string) => logger.info(message.trim()),
  },
}));
app.use(express.json());
app.use(express.urlencoded({ extended: true }));
app.use(rateLimiter);
app.use('/api', routes);

app.use('/api/*', (req: Request, res: Response) => {
  res.status(404).json({
    success: false,
    error: {
      message: `Route not found: ${req.method} ${req.originalUrl}`,
      statusCode: 404,
    },
  });
});

app.use(errorHandler);

const connectedClients = new Map<string, any>();

io.on('connection', (socket) => {
  logger.info(`Client connected: ${socket.id}`);
  connectedClients.set(socket.id, socket);
  socket.emit('signals', signalEngine.getActiveSignals());
  socket.emit('portfolio', riskManager.getPortfolio());

  socket.on('subscribe', (symbols: string[]) => {
    logger.info(`Client ${socket.id} subscribed to: ${symbols}`);
    symbols.forEach((symbol) => socket.join(`symbol:${symbol}`));
  });

  socket.on('unsubscribe', (symbols: string[]) => {
    symbols.forEach((symbol) => socket.leave(`symbol:${symbol}`));
  });

  socket.emit('sheet:data', marketScanner.getLastData());

  socket.on('disconnect', () => {
    logger.info(`Client disconnected: ${socket.id}`);
    connectedClients.delete(socket.id);
  });
});

let lastTickerBroadcast = 0;
const TICKER_BROADCAST_INTERVAL = 1000;

websocketService.on('ticker', (data: MarketData) => {
  io.to(`symbol:${data.symbol}`).emit('ticker', data);
  const now = Date.now();
  if (now - lastTickerBroadcast >= TICKER_BROADCAST_INTERVAL) {
    lastTickerBroadcast = now;
    io.emit('ticker', data);
  }
});

websocketService.on('kline', (data: any) => {
  io.to(`symbol:${data.symbol}`).emit('kline', data);
  io.emit('kline', data);
});

websocketService.on('depth', (data: any) => {
  io.to(`symbol:${data.symbol}`).emit('depth', data);
});

websocketService.on('trade', (data: any) => {
  io.to(`symbol:${data.symbol}`).emit('trade', data);
});

// Backend only relays canonical Python signals; it never recalculates trading decisions.
signalEngine.on('signal', (signal) => {
  io.emit('signal', { ...signal, authority: 'python' });
  logger.info(`Relayed canonical signal: ${signal.type} ${signal.symbol}`);
});

signalEngine.on('signal_update', (signal) => {
  io.emit('signal_update', { ...signal, authority: 'python' });
});

marketScanner.on('scan', (data) => {
  io.emit('sheet:data', data);
});

async function startServices() {
  try {
    websocketService.connect(['!ticker@arr']);

    // These services remain available for read-only UI compatibility. They are
    // deliberately not started as independent trading/signal authorities.
    logger.info('Canonical signal authority: Python');
    logger.info('Node signal generation/scanning/trade simulation is disabled as an execution authority.');

    await marketScanner.discoverSymbols();

    httpServer.listen(config.port, () => {
      logger.info(`🚀 DeltaTerminal Backend running on port ${config.port}`);
      logger.info(`📊 Environment: ${config.nodeEnv}`);
      logger.info(`🔗 API: http://localhost:${config.port}/api`);
      logger.info(`🔌 WebSocket: ws://localhost:${config.port}`);
    });
  } catch (error) {
    logger.error('Failed to start services:', error);
    process.exit(1);
  }
}

process.on('SIGTERM', () => {
  logger.info('SIGTERM received, shutting down gracefully...');
  websocketService.disconnect();
  signalEngine.stopContinuousScan();
  marketScanner.stop();
  httpServer.close(() => process.exit(0));
});

process.on('SIGINT', () => {
  logger.info('SIGINT received, shutting down...');
  websocketService.disconnect();
  signalEngine.stopContinuousScan();
  marketScanner.stop();
  httpServer.close(() => process.exit(0));
});

startServices();

export { app, io };
