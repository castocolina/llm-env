import { compareEvents } from '../util/key.js';

// Sorted copy of the events; the dedupe stage relies on this order.
export const sortEvents = (events) => events.slice().sort(compareEvents);
