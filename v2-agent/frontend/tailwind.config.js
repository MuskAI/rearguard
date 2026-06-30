/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // 墨 — 冷静取证工作台层级
        ink: {
          950: "#15211e",
          900: "#f3f7f4",
          800: "#ffffff",
          700: "#dce6e0",
          600: "#b9c8c0",
          500: "#60716a",
        },
        // 朱砂 / 青玉 / 证据金
        cinnabar: { DEFAULT: "#c43d2f", light: "#d85a48", dark: "#933026" },
        jade: { DEFAULT: "#238f82", light: "#2fa99b", dark: "#19675e" },
        gold: { DEFAULT: "#9a7337", light: "#b88c44", dark: "#705328" },
        rice: "#172334", // 标题正文
        // 兼容旧 token 名 → 映射到新主色，配色一改即全局生效
        brand: {
          cyan: "#238f82", // 青玉
          blue: "#255f85", // 深湖蓝
          magenta: "#9a7337", // 证据金（次强调）
        },
        verdict: {
          real: "#238f82", // 真 → 青玉
          warn: "#c78324", // 存疑 → 琥珀
          fake: "#c43d2f", // 伪 → 朱砂
        },
      },
      fontFamily: {
        serif: ['"Songti SC"', '"STSong"', '"Source Han Serif SC"', '"Noto Serif SC"', "serif"],
        sans: ["system-ui", "-apple-system", '"PingFang SC"', '"Microsoft YaHei"', "sans-serif"],
        mono: ['"SF Mono"', "ui-monospace", "Menlo", "monospace"],
      },
      boxShadow: {
        seal: "0 0 0 1px rgba(216,65,47,0.25), 0 8px 30px -12px rgba(216,65,47,0.35)",
      },
    },
  },
  plugins: [],
};
