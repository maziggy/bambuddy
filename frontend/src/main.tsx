import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './pwa' // capture beforeinstallprompt before React mounts (#1460)
import './index.css'
import './i18n' // Initialize i18n
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
