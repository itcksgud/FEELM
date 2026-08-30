export const localFeaturesEnabled = import.meta.env.DEV || import.meta.env.VITE_LOCAL_FEATURES_ENABLED === "true";

export function isLoopbackOrigin(origin = globalThis.location?.origin ?? "") {
  return /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/i.test(origin);
}
