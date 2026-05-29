/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: "#0EA37F",
        ink: { 50: "#F8FAFB", 100: "#EEF1F4", 200: "#D5DCE2",
               500: "#5B6A78", 700: "#1E2A35", 900: "#0B131C" },
        warm: "#10B981",
        flat: "#94A3B8",
        cool: "#3B82F6",
        tire: "#F97316",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "sans-serif"],
      },
    },
  },
  plugins: [],
};
