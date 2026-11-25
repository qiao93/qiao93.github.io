import './styles/index.css';
import { Suspense, lazy } from 'react';
import { Routes, Route } from 'react-router-dom';
import { HelmetProvider } from 'react-helmet-async';
import Layout from './components/Layout';
import Index from './pages/index';

const Summary = lazy(() => import('./pages/summary'));
const AnalysisList = lazy(() => import('@/pages/analysis'));
const AnalysisDetail = lazy(() => import('@/pages/analysis/detail'));

export default function ClassicTheme() {
  return (
    <HelmetProvider>
      <Suspense fallback={<div>Loading...</div>}>
        <Routes>
          <Route path="/" element={<Index />} />
          <Route path="/summary" element={<Summary />} />
          <Route
            path="/analysis"
            element={
              <Layout>
                <AnalysisList />
              </Layout>
            }
          />
          <Route
            path="/analysis/:slug"
            element={
              <Layout>
                <AnalysisDetail />
              </Layout>
            }
          />
          <Route path="*" element={<Index />} />
        </Routes>
      </Suspense>
    </HelmetProvider>
  );
}
