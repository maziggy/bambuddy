/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Bambu Lab color palette
        bambu: {
          green: '#00ae42',
          'green-light': '#00c64d',
          'green-dark': '#009438',
          dark: '#1a1a1a',
          'dark-secondary': '#2d2d2d',
          'dark-tertiary': '#3d3d3d',
          gray: '#808080',
          'gray-light': '#a0a0a0',
          'gray-dark': '#4a4a4a',
        }
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
