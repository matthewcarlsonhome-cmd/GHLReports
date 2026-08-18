/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: "#fcfcfb",
        plane: "#f9f9f7",
        ink: "#0b0b0b",
        "ink-2": "#52514e",
        muted: "#898781",
        grid: "#e1e0d9",
        hairline: "#c3c2b7",
        series: "#2a78d6",
        "status-good": "#0ca30c",
        "status-good-text": "#006300",
        "status-warning": "#fab219",
        "status-critical": "#d03b3b",
      },
      fontSize: {
        xxs: ["0.6875rem", "1rem"],
      },
    },
  },
  plugins: [],
};
