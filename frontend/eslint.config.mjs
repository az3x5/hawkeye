import next from "eslint-config-next";

/** Flat config: eslint-config-next exports an array of configs. */
const config = [
  ...next,
  { ignores: [".next/**", "node_modules/**", "next-env.d.ts"] },
];

export default config;
