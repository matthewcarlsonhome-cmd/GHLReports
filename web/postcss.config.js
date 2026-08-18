// PostCSS pipeline (run by Vite on every CSS file): Tailwind expands utility
// classes into real CSS, then Autoprefixer adds vendor prefixes as needed.
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
