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

// Create Express app
const app = express();
const httpServer = createServer(app);

// Create Socket.IO server
const io = new SocketIOServer(httpServer, {
  cors: {
    origin: ['http://localhost:5173', 'http://localhost:3000'],
    methods: ['GET', 'POST'],
    credentials: true,
  },
  pingTimeout: config.websocket.pingTimeout,
  pingInterval: config.websocket.pingInterval,
});

// Middleware
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

// Routes
app.use('/api', routes);

// 404 handler for unknown API routes
app.use('/api/*', (req: Request, res: Response) => {
  res.status(404).json({
    success: false,
    error: {
      message: `Route not found: ${req.method} ${req.originalUrl}`,
      statusCode: 404,
    },
  });
});

// Error handling
app.use(errorHandler);

// WebSocket handling
const connectedClients = new Map<string, any>();

io.on('connection', (socket) => {
  logger.info(`Client connected: ${socket.id}`);
  connectedClients.set(socket.id, socket);

  // Send current signals immediately on connect
  socket.emit('signals', signalEngine.getActiveSignals());

  // Send portfolio updates immediately on connect
  socket.emit('portfolio', riskManager.getPortfolio());

  // Handle client subscriptions (per-symbol rooms)
  socket.on('subscribe', (symbols: string[]) => {
    logger.info(`Client ${socket.id} subscribed to: ${symbols}`);
    symbols.forEach((symbol) => {
      socket.join(`symbol:${symbol}`);
    });
  });

  socket.on('unsubscribe', (symbols: string[]) => {
    symbols.forEach((symbol) => {
      socket.leave(`symbol:${symbol}`);
    });
  });

// Send current scanner data immediately on connect
  socket.emit('sheet:data', marketScanner.getLastData());

    // Handle disconnect
  socket.on('disconnect', () => {
    logger.info(`Client disconnected: ${socket.id}`);
    connectedClients.delete(socket.id);
  });
});

// WebSocket event handlers — broadcast to ALL connected clients
let lastTickerBroadcast = 0;
const TICKER_BROADCAST_INTERVAL = 1000; // 1 second rate limit for ticker broadcasts

websocketService.on('ticker', (data: MarketData) => {
  // Broadcast to per-symbol subscribers
  io.to(`symbol:${data.symbol}`).emit('ticker', data);
  
  // Broadcast globally to all clients (throttled)
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

// Signal engine events
signalEngine.on('signal', (signal) => {
  io.emit('signal', signal);
  logger.info(`New signal: ${signal.type} ${signal.symbol}`);
});

signalEngine.on('signal_update', (signal) => {
  io.emit('signal_update', signal);
});

// Market scanner events — broadcast full market data to all clients
marketScanner.on('scan', (data) => {
  io.emit('sheet:data', data);
});

// Start services
async function startServices() {
  try {
    // Connect WebSocket for real-time data
    websocketService.connect(['!ticker@arr']);
    
    // Start continuous market scanning
    signalEngine.startContinuousScan(60000, {
      minConfidence: 0.5,
      maxSignals: 10,
    });

    // Start full market scanner (every 15 seconds)
    await marketScanner.discoverSymbols();
    marketScanner.start(15000);

    // Start trade simulator (listens for signals, monitors SL/TP)
    tradeSimulator.start();
    logger.info('📈 Trade Simulator active — paper trading all signals');

    // Start server
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

// Graceful shutdown
process.on('SIGTERM', () => {
  logger.info('SIGTERM received, shutting down gracefully...');
  websocketService.disconnect();
  signalEngine.stopContinuousScan();
  marketScanner.stop();
  httpServer.close(() => {
    logger.info('Server closed');
    process.exit(0);
  });
});

process.on('SIGINT', () => {
  logger.info('SIGINT received, shutting down...');
  websocketService.disconnect();
  signalEngine.stopContinuousScan();
  marketScanner.stop();
  httpServer.close(() => {
    process.exit(0);
  });
});

// Start the application
startServices();

export { app, io };
