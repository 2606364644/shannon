/**
 * Provider registration — injects custom providers into the DI container.
 *
 * Called once at worker startup via side-effect import in worker.ts.
 * Overrides the default container factory to include ReportTranslationProvider.
 */

import type { SessionMetadata } from '../audit/utils.js';
import { Container, setContainerFactory } from '../services/container.js';
import type { ContainerConfig } from '../types/config.js';
import { ReportTranslationProvider } from './report-translation-provider.js';

/**
 * Create a Container with the translation provider injected.
 * Matches the setContainerFactory() parameter signature:
 * (workflowId: string, sessionMetadata: SessionMetadata, config: ContainerConfig) => Container
 */
function createContainerWithTranslation(
  _workflowId: string,
  sessionMetadata: SessionMetadata,
  config: ContainerConfig,
): Container {
  return new Container({
    sessionMetadata,
    config,
    reportOutputProvider: new ReportTranslationProvider(),
  });
}

/**
 * Register custom providers by overriding the container factory.
 *
 * Call once at worker startup. Subsequent calls overwrite the previous factory.
 */
export function registerProviders(): void {
  setContainerFactory(createContainerWithTranslation);
}
