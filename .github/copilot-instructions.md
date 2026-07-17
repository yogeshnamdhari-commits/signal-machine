# DeltaTerminal - AI-Powered Binance Futures Scanner

## Project Overview
Institutional-grade cryptocurrency trading platform with real-time market scanning and AI-powered signal detection.

## Tech Stack
- **Backend**: Node.js + TypeScript + Express + Socket.IO + Prisma + PostgreSQL
- **Frontend**: React + TypeScript + Vite + TailwindCSS + TradingView Lightweight Charts
- **AI Engine**: Python + scikit-learn + pandas + ta-lib

## Architecture
- Monorepo structure: `packages/backend`, `packages/frontend`, `packages/ai-engine`
- Real-time WebSocket connections for market data
- RESTful API for portfolio and signal management
- PostgreSQL for persistence (signals, alerts, portfolio history)

## Development Commands
```bash
# Install all dependencies
npm run install:all

# Start development
npm run dev

# Run tests
npm test

# Build for production
npm run build
```

## Coding Standards
- TypeScript strict mode enabled
- ESLint + Prettier for code formatting
- Jest for testing
- Use functional components in React
- Follow SOLID principles in backend
