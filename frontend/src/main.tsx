import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import './styles/tokens.css'
import './styles/views.css'
import './styles/tactile.css'

import App from './App'
import Home from './views/Home'
import Inbox from './views/Inbox'
import Project from './views/Project'
import Design from './views/Design'
import Gallery from './views/Gallery'
import Studies from './views/Studies'
import Studio from './views/Studio'
import Login from './views/Login'
import PublicGallery from './views/PublicGallery'

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
      { path: 'inbox', element: <Inbox /> },
      { path: 'p/:projectId', element: <Project /> },
      { path: 'd/:designId', element: <Design /> },
      { path: 'd/:designId/study/:studyId', element: <Studio /> },
      { path: 'gallery', element: <Gallery /> },
      { path: 'studies', element: <Studies /> },
    ],
  },
  { path: '/login', element: <Login /> },
  // PUBLIC gallery: no auth, no app shell
  { path: '/s/:slug', element: <PublicGallery /> },
])

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
)
