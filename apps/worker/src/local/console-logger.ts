import type { ActivityLogger } from '../types/activity-logger.js';

export class ConsoleActivityLogger implements ActivityLogger {
  info(message: string, attrs?: Record<string, unknown>): void {
    if (attrs && Object.keys(attrs).length > 0) {
      console.log(`[INFO] ${message}`, JSON.stringify(attrs));
    } else {
      console.log(`[INFO] ${message}`);
    }
  }

  warn(message: string, attrs?: Record<string, unknown>): void {
    if (attrs && Object.keys(attrs).length > 0) {
      console.warn(`[WARN] ${message}`, JSON.stringify(attrs));
    } else {
      console.warn(`[WARN] ${message}`);
    }
  }

  error(message: string, attrs?: Record<string, unknown>): void {
    if (attrs && Object.keys(attrs).length > 0) {
      console.error(`[ERROR] ${message}`, JSON.stringify(attrs));
    } else {
      console.error(`[ERROR] ${message}`);
    }
  }
}
