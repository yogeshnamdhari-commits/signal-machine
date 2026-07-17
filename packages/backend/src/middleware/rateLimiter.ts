import { Request, Response, NextFunction } from 'express';
import { config } from '../config';

// Simple in-memory rate limiter
const requestCounts = new Map<string, { count: number; resetTime: number }>();

export const rateLimiter = (req: Request, res: Response, next: NextFunction): void => {
  // Bypass rate limiting for localhost in development
  const clientIp = req.ip || req.connection.remoteAddress || 'unknown';
  if (config.nodeEnv === 'development' && (clientIp === '::1' || clientIp === '127.0.0.1' || clientIp === '::ffff:127.0.0.1')) {
    next();
    return;
  }
  const now = Date.now();
  const windowMs = config.rateLimit.windowMs;
  const maxRequests = config.rateLimit.max;

  const clientData = requestCounts.get(clientIp);

  if (!clientData || now > clientData.resetTime) {
    // New window
    requestCounts.set(clientIp, {
      count: 1,
      resetTime: now + windowMs,
    });
    next();
    return;
  }

  if (clientData.count >= maxRequests) {
    res.status(429).json({
      success: false,
      error: {
        message: 'Too many requests, please try again later',
        statusCode: 429,
        retryAfter: Math.ceil((clientData.resetTime - now) / 1000),
      },
    });
    return;
  }

  clientData.count++;
  next();
};

// Clean up old entries periodically
setInterval(() => {
  const now = Date.now();
  for (const [ip, data] of requestCounts.entries()) {
    if (now > data.resetTime) {
      requestCounts.delete(ip);
    }
  }
}, 60000);
