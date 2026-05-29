/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Warm off-white paper ground + raised surface (neutrals tinted toward
        // the brand green hue ~164 for subconscious cohesion).
        paper: "oklch(0.972 0.006 150)",
        surface: "oklch(0.995 0.004 150)",
        brand: {
          DEFAULT: "oklch(0.605 0.122 164)",
          dark: "oklch(0.52 0.12 164)",
          ink: "oklch(0.34 0.06 164)",
          soft: "oklch(0.95 0.035 164)",
        },
        ink: {
          50: "oklch(0.972 0.006 150)",
          100: "oklch(0.94 0.008 156)",
          200: "oklch(0.885 0.011 158)",
          300: "oklch(0.80 0.013 160)",
          500: "oklch(0.555 0.018 162)",
          700: "oklch(0.36 0.022 164)",
          900: "oklch(0.22 0.024 165)",
        },
        // Semantic relationship-arc + tier hues (the only colors besides brand).
        warm: "oklch(0.60 0.13 158)",
        flat: "oklch(0.62 0.012 160)",
        cool: "oklch(0.58 0.105 245)",
        tire: "oklch(0.66 0.12 62)",
      },
      fontFamily: {
        sans: ['"Hanken Grotesk"', "system-ui", "-apple-system", "sans-serif"],
        display: ['"Bricolage Grotesque"', '"Hanken Grotesk"', "sans-serif"],
      },
      fontSize: {
        // Fixed rem scale (app UI), ~1.28 ratio, fewer steps + more contrast.
        xs: ["0.75rem", { lineHeight: "1.1rem" }],
        sm: ["0.8125rem", { lineHeight: "1.25rem" }],
        base: ["0.9375rem", { lineHeight: "1.5rem" }],
        lg: ["1.1875rem", { lineHeight: "1.55rem" }],
        xl: ["1.55rem", { lineHeight: "1.7rem" }],
        "2xl": ["2.05rem", { lineHeight: "2.15rem" }],
        "3xl": ["2.7rem", { lineHeight: "2.7rem" }],
      },
      boxShadow: {
        card: "0 1px 2px oklch(0.22 0.024 165 / 0.04), 0 1px 0 oklch(0.22 0.024 165 / 0.02)",
        lift: "0 6px 24px -8px oklch(0.22 0.024 165 / 0.14)",
      },
      borderRadius: { card: "0.5rem" },
    },
  },
  plugins: [],
};
