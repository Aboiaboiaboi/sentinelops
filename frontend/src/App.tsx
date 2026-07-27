import { lazy } from 'react';
import { Route, Routes } from 'react-router-dom';
import { AppLayout, AuthLayout } from '@/components/AppLayout';
import { IndexRedirect, ProtectedRoute } from '@/components/ProtectedRoute';
import DashboardPage from '@/pages/DashboardPage';
import LoginPage from '@/pages/LoginPage';
import ProjectPage from '@/pages/ProjectPage';
import SignupPage from '@/pages/SignupPage';

// The only two screens that render charts, and Recharts is over half the bundle.
// Split out, it no longer ships to the login screen — which is every user's first
// paint and needs none of it. AppLayout supplies the Suspense boundary.
const ScanPage = lazy(() => import('@/pages/ScanPage'));
const ReportPage = lazy(() => import('@/pages/ReportPage'));

function NotFound() {
  return (
    <div className="py-16 text-center">
      <h1 className="text-2xl font-semibold">Page not found</h1>
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route element={<AuthLayout />}>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/signup" element={<SignupPage />} />
      </Route>

      <Route element={<ProtectedRoute />}>
        <Route element={<AppLayout />}>
          <Route path="/" element={<IndexRedirect />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/projects/:projectId" element={<ProjectPage />} />
          <Route path="/scans/:scanId" element={<ScanPage />} />
          <Route path="/scans/:scanId/report" element={<ReportPage />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Route>
    </Routes>
  );
}
