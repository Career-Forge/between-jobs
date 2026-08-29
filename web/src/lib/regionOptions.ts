// Mirrors forge-engines' `locale_profiles_v2.json` profile keys + display
// names (R4/R6, resumeforge-shape-and-fit.md) -- hand-kept in sync, same
// "small and stable enough to hand-mirror" reasoning as this file's
// siblings (headerComposerTypes.ts). Japan/China are deliberately absent
// (decision #8): forge-engines resolves them to DEFAULT with an
// explanatory note rather than a real profile, so offering them here
// would promise a customization this platform doesn't have yet.
export interface RegionOption {
  code: string;
  label: string;
}

export const REGION_OPTIONS: RegionOption[] = [
  { code: "US", label: "United States" },
  { code: "CA", label: "Canada" },
  { code: "UK", label: "United Kingdom" },
  { code: "IE", label: "Ireland" },
  { code: "NL", label: "Netherlands" },
  { code: "NORDICS", label: "Nordics (Sweden, Norway, Denmark, Finland)" },
  { code: "FR", label: "France" },
  { code: "DE", label: "Germany" },
  { code: "AT", label: "Austria" },
  { code: "CH", label: "Switzerland" },
  { code: "MX", label: "Mexico" },
  { code: "AU", label: "Australia" },
  { code: "NZ", label: "New Zealand" },
  { code: "IN", label: "India" },
  { code: "BR", label: "Brazil" },
  { code: "GULF", label: "Gulf (UAE, Saudi Arabia, Qatar)" },
  { code: "SEA_HUB", label: "Southeast Asia (Singapore, Malaysia)" },
  { code: "DEFAULT", label: "Generic international" },
];
