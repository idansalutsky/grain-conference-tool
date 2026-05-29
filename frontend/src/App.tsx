import { Routes, Route, Navigate } from "react-router-dom";
import { Layout } from "./components/Layout";
import { TodayPage } from "./pages/Today";
import { ConferencesPage } from "./pages/Conferences";
import { ConferenceDetailPage } from "./pages/ConferenceDetail";
import { PlanningPage } from "./pages/Planning";
import { CapturePage } from "./pages/Capture";
import { ContactsPage } from "./pages/Contacts";
import { ContactDetailPage } from "./pages/ContactDetail";
import { NudgesPage } from "./pages/Nudges";
import { SettingsPage } from "./pages/Settings";
import { DiscoveryPage } from "./pages/Discovery";
import { CompaniesPage } from "./pages/Companies";
import { CompanyDetailPage } from "./pages/CompanyDetail";
import { TeamPage } from "./pages/Team";
import { BrainPage } from "./pages/Brain";

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Navigate to="/today" replace />} />
        <Route path="/today" element={<TodayPage />} />
        <Route path="/brain" element={<BrainPage />} />
        <Route path="/conferences" element={<ConferencesPage />} />
        <Route path="/conferences/:id" element={<ConferenceDetailPage />} />
        <Route path="/companies" element={<CompaniesPage />} />
        <Route path="/companies/:id" element={<CompanyDetailPage />} />
        <Route path="/planning" element={<PlanningPage />} />
        <Route path="/capture" element={<CapturePage />} />
        <Route path="/contacts" element={<ContactsPage />} />
        <Route path="/contacts/:id" element={<ContactDetailPage />} />
        <Route path="/nudges" element={<NudgesPage />} />
        <Route path="/discovery" element={<DiscoveryPage />} />
        <Route path="/team" element={<TeamPage />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Routes>
    </Layout>
  );
}
