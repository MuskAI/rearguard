import { createRoot, Root } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import ErrorBoundary, { StartupError } from './ErrorBoundary.tsx'

const rootElement = document.getElementById('root')
let appRoot: Root | null = null

function renderStartupError(error: unknown) {
  if (!rootElement) return
  appRoot = appRoot || createRoot(rootElement)
  appRoot.render(<StartupError error={error} />)
}

window.addEventListener('error', (event) => {
  renderStartupError(event.error || event.message)
})

window.addEventListener('unhandledrejection', (event) => {
  renderStartupError(event.reason || '未处理的异步异常')
})

try {
  if (!rootElement) throw new Error('Root element #root not found')
  appRoot = createRoot(rootElement)
  appRoot.render(
    <ErrorBoundary>
      <App />
    </ErrorBoundary>,
  )
} catch (error) {
  renderStartupError(error)
}
