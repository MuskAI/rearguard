/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // 墨 — 浅色宣纸工作台层级
        ink: {
          950: "#172334",
          900: "#fbf7ef",
          800: "#ffffff",
          700: "#efe4d4",
          600: "#d9c8b0",
          500: "#7d6f5e",
        },
        // 朱砂（印章主调）/ 青玉 / 鎏金
        cinnabar: { DEFAULT: "#c43d2f", light: "#d85a48", dark: "#933026" },
        jade: { DEFAULT: "#238f82", light: "#2fa99b", dark: "#19675e" },
        gold: { DEFAULT: "#a7833e", light: "#c29b52", dark: "#80652f" },
        rice: "#172334", // 标题正文
        // 兼容旧 token 名 → 映射到新主色，配色一改即全局生效
        brand: {
          cyan: "#238f82", // 青玉
          blue: "#c43d2f", // 朱砂（主操作）
          magenta: "#a7833e", // 鎏金（次强调）
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
