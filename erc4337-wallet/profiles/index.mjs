import {profile as slhDsaV1} from './slh-dsa-v1.mjs';
export const DEFAULT_PROFILE = slhDsaV1.id;
const profiles = new Map([[slhDsaV1.id, slhDsaV1]]);
export function getProfile(id = DEFAULT_PROFILE) {
  const profile = profiles.get(id);
  if (!profile) throw new Error(`Unsupported wallet profile: ${id}`);
  return profile;
}
