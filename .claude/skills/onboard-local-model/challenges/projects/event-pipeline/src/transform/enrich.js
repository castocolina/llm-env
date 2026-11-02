import { isoDay, isoHour } from '../util/time.js';

// Adds calendar fields used by the reports; the original fields are untouched.
export const enrichEvent = (e) => ({ ...e, day: isoDay(e.ts), hour: isoHour(e.ts) });

export const enrichAll = (events) => events.map(enrichEvent);

// Weekday name in UTC, used by the weekly report.
const DAYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'];
export const weekday = (ts) => DAYS[new Date(ts).getUTCDay()];

export const isWeekend = (ts) => ['sat', 'sun'].includes(weekday(ts));
