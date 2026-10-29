import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import AuthBoundary from './AuthBoundary'
import './styles.css'

createRoot(document.getElementById('root')!).render(
  <React.StrictMode><AuthBoundary><App /></AuthBoundary></React.StrictMode>
)
