// Compatibility test adapter. The platform broker itself has no question dependency.
import { QuestionStore } from '../../extensions/QAInteract/src/storage.js';
import { questionRoutes } from '../../extensions/QAInteract/src/routes.js';
import { startPlatformBroker } from '../../src/shared/broker.js';
export type { Endpoint } from '../../src/shared/broker.js';
export function startBroker(store: QuestionStore, onFocus: (id?: string) => void) {
 return startPlatformBroker(store.directory, { show: onFocus, route: questionRoutes(store, onFocus), ensureModule: () => {} });
}
