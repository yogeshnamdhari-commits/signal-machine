import dotenv from 'dotenv';
import path from 'path';

dotenv.config({ path: path.resolve(__dirname, '../../.env') });

export const config = {
  port: parseInt(process.env.PORT || '3001', 10),
  nodeEnv: process.env.NODE_ENV || 'development',
  
  // Database
  database: {
    url: process.env.DATABASE_URL || 'postgresql://localhost:5432/deltaterminal',
  },

  // Binance API
  binance: {
    apiKey: process.env.BINANCE_API_KEY || '',
    apiSecret: process.env.BINANCE_API_SECRET || '',
    testnet: process.env.BINANCE_TESTNET === 'true',
    wsUrl: process.env.BINANCE_WS_URL || 'wss://fstream.binance.com',
  },

  // AI Engine
  aiEngine: {
    url: process.env.AI_ENGINE_URL || 'http://localhost:8000',
  },

  // JWT
  jwt: {
    secret: process.env.JWT_SECRET || 'your-secret-key-change-in-production',
    expiresIn: '24h',
  },

  // Rate limiting
  rateLimit: {
    windowMs: 15 * 60 * 1000, // 15 minutes
    max: 100,
  },

  // WebSocket
  websocket: {
    pingTimeout: 60000,
    pingInterval: 25000,
  },
};

export type Config = typeof config;
