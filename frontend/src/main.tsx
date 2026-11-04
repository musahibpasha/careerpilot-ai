import React from 'react'
import { createRoot } from 'react-dom/client'
import AuthBoundary from './AuthBoundary'
import './styles.css'

createRoot(document.getElementById('root')!).render(
  <React.StrictMode><AuthBoundary /></React.StrictMode>
)
