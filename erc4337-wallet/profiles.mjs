import {profile as falconV3} from './v3/profile.mjs';
import {profile as slhDsaV1} from './v1/profile.mjs';
import {profile as slhDsaV2} from './v2/profile.mjs';
export const DEFAULT_PROFILE = slhDsaV1.id;
const profiles = new Map([[slhDsaV1.id, slhDsaV1], [slhDsaV2.id, slhDsaV2], [falconV3.id,falconV3]]);
export function getProfile(id = DEFAULT_PROFILE) {
  const profile = profiles.get(id);
  if (!profile) throw new Error(`Unsupported wallet profile: ${id}`);
  return profile;
}

// Version is the experiment chain, not the EntryPoint or algorithm standard version.
const versions = new Map([['v1', slhDsaV1.id], ['v2', slhDsaV2.id], ['v3',falconV3.id]]);
export function selectProfile({version, profile} = {}) {
  const selected = version === undefined ? undefined : versions.get(version);
  if (version !== undefined && !selected) throw new Error(`Unsupported experiment version: ${version}`);
  if (selected && profile && selected !== profile) throw new Error('Conflicting --version and --profile');
  return getProfile(profile || selected || DEFAULT_PROFILE).id;
}
