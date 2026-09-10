import { lazy } from 'react';
import { Link, Navigate, Route, Routes } from 'react-router-dom';
import { AppLayout, AuthLayout } from '@/components/AppLayout';
import { PageHeader } from '@/components/PageHeader';
import { MarketingLayout } from '@/components/marketing/MarketingLayout';
import { ProtectedRoute } from '@/components/ProtectedRoute';
import DashboardPage from '@/pages/DashboardPage';
import LoginPage from '@/pages/LoginPage';
import ProjectPage from '@/pages/ProjectPage';
import SignupPage from '@/pages/SignupPage';

// The only two screens that render charts, and Recharts is over half the bundle.
// Split out, it no longer ships to the login screen — which is every user's first
// paint and needs none of it. AppLayout supplies the Suspense boundary.
const ScanPage = lazy(() => import('@/pages/ScanPage'));
const ReportPage = lazy(() => import('@/pages/ReportPage'));

// The public marketing pages pull in GSAP — split out for the same reason as
// above, so neither the login screen nor the app itself ships animation
// tooling it never uses.
const LandingPage = lazy(() => import('@/pages/LandingPage'));
const HowItWorksPage = lazy(() => import('@/pages/HowItWorksPage'));
const WhoItsForPage = lazy(() => import('@/pages/WhoItsForPage'));

function NotFound() {
  return (
    <div className="py-16">
      <PageHeader
        eyebrow="404"
        caption={
          <Link
            to="/dashboard"
            className="text-primary-bright underline-offset-4 hover:underline"
          >
            Back to projects
          </Link>
        }
      >
        Page not found
      </PageHeader>
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      {/* Public — reachable logged out. useSession() inside MarketingLayout
          resolves a 401 to null rather than redirecting, so these render
          for anyone; the nav CTA just swaps based on session state. */}
      <Route path="/" element={<Navigate to="/home" replace />} />
      <Route element={<MarketingLayout />}>
        <Route path="/home" element={<LandingPage />} />
        <Route path="/how-it-works" element={<HowItWorksPage />} />
        <Route path="/who-its-for" element={<WhoItsForPage />} />
      </Route>

      <Route element={<AuthLayout />}>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/signup" element={<SignupPage />} />
      </Route>

      <Route element={<ProtectedRoute />}>
        <Route element={<AppLayout />}>
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
