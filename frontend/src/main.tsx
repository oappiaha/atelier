import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import './styles/tokens.css'
import './styles/views.css'
import './styles/tactile.css'

import App from './App'
import Home from './views/Home'
import Login from './views/Login'

// staleTime 5 min: presigned image URLs in responses are now stable for days
// (server-side memo), so refetches are cheap but still pointless every 30s —
// window-focus refetch keeps a long-lived PWA fresh.
const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 300_000, retry: 1 } },
})

// Routes per TDD §10.1
const router = createBrowserRouter([
  {
    path: '/',
    element: <App />,
    children: [
      { index: true, element: <Home /> },
      { path: 'inbox', lazy: async () => ({ Component: (await import('./views/Inbox')).default }) },
      { path: 'p/:projectId', lazy: async () => ({ Component: (await import('./views/Project')).default }) },
      { path: 'd/:designId', lazy: async () => ({ Component: (await import('./views/Design')).default }) },
      { path: 'd/:designId/studio', lazy: async () => ({ Component: (await import('./views/StudioHub')).default }) },
      { path: 'd/:designId/study/:studyId', lazy: async () => ({ Component: (await import('./views/Studio')).default }) },
      { path: 'gallery', lazy: async () => ({ Component: (await import('./views/Gallery')).default }) },
      { path: 'studies', lazy: async () => ({ Component: (await import('./views/Studies')).default }) },
    ],
  },
  { path: '/login', element: <Login /> },
  // PUBLIC gallery: no auth, no app shell
  { path: '/s/:slug', lazy: async () => ({ Component: (await import('./views/PublicGallery')).default }) },
])

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
)
