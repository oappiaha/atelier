import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import './styles/tokens.css'
import './styles/tactile.css'

import App from './App'
import Home from './views/Home'
import Project from './views/Project'
import Design from './views/Design'
import Gallery from './views/Gallery'
import Studio from './views/Studio'
import Login from './views/Login'
import PublicGallery from './views/PublicGallery'

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1 } },
})

// Routes per TDD §10.1
const router = createBrowserRouter([
  {
    path: '/',
    element: <App />,
    children: [
      { index: true, element: <Home /> },
      { path: 'p/:projectId', element: <Project /> },
      { path: 'd/:designId', element: <Design /> },
      { path: 'd/:designId/study/:studyId', element: <Studio /> },
      { path: 'gallery', element: <Gallery /> },
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
