import express, { Request, Response, NextFunction } from 'express';
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

const CANONICAL_SIGNAL_AUTHORITY = process.env.CANONICAL_SIGNAL_AUTHORITY ?? 'python';
if (CANONICAL_SIGNAL_AUTHORITY !== 'python') {
  throw new Error('Unsafe configuration: Python must be the canonical signal authority');
}

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

// Node is an integration/UI layer. Any endpoint capable of generating, mutating,
// or simulating an executable trading decision is fail-closed here. Canonical
// signal/risk decisions originate in Python and are consumed downstream.
const canonicalReadOnlyGuard = (req: Request, res: Response, next: NextFunction) => {
  const blocked = new Set([
    'POST /api/signals/scan',
    'PUT /api/signals/:id/status',
    'POST /api/indicators/signal',
    'PUT /api/risk/params',
    'POST /api/risk/position/check',
    'POST /api/risk/position/size',
    'POST /api/scanner/scan',
    'POST /api/simulator/reset',
  ]);
  const key = `${req.method} ${req.baseUrl}${req.path}`;
  if (blocked.has(key)) {
    return res.status(409).json({
      success: false,
      error: {
        code: 'PYTHON_CANONICAL_AUTHORITY',
        message: 'Trading decisions and risk mutations are owned exclusively by the Python engine.',
      },
    });
  }
  return next();
};

app.use('/api', canonicalReadOnlyGuard);
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
    logger.info('Canonical signal authority: Python');
    logger.info('Node executable signal generation, risk mutation, scanning and simulation are disabled.');
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
  marketScanner.stop();
  httpServer.close(() => process.exit(0));
});

process.on('SIGINT', () => {
  logger.info('SIGINT received, shutting down...');
  websocketService.disconnect();
  marketScanner.stop();
  httpServer.close(() => process.exit(0));
});

startServices();

export { app, io };
