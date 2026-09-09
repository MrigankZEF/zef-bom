// The three volume tiers, in one place.
//
// These constants were declared six times over — Tree, PartDrawer, Costing, Catalog,
// Facilities and a stray `tierLabel` in Pending — each with its own copy of the same comment
// pointing at Tree.jsx as the original. Six copies of a number is how two controls end up
// disagreeing about which tier you are looking at.
export const TIERS = [1, 100, 10000];

// The tier the app opens on. 10k is the volume the business case is actually written at, so
// opening anywhere else means every screen's first number is one nobody quotes.
//
// Deliberately NOT persisted. A remembered tier means the number you see depends on what you
// last clicked, possibly weeks ago, and there is nothing on screen saying so. Every load
// starts here. (The API's own default stays 100 — this is the UI's opening choice, not a
// change to the backend.)
export const DEFAULT_TIER = 10000;

export const tierLabel = (v) => (v >= 1000 ? `${v / 1000}k` : `${v}`);
