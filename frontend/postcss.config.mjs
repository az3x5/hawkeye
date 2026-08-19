/** Tailwind v4 runs as a PostCSS plugin; without this the bundle ships with
 *  no utility classes at all. */
const config = {
  plugins: { "@tailwindcss/postcss": {} },
};

export default config;
