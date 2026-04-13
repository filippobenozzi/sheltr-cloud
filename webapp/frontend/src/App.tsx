import { Navigate, Route, Routes } from "react-router-dom"

import { NotificationCenter } from "@/components/ui/notification-center"
import { ConfigPage } from "@/pages/config-page"
import { ControlPage } from "@/pages/control-page"
import { HomePage } from "@/pages/home-page"

export default function App() {
  return (
    <>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/config" element={<ConfigPage />} />
        <Route path="/instance/:instanceId/config" element={<ConfigPage />} />
        <Route path="/control" element={<ControlPage />} />
        <Route path="/control/:instanceId" element={<ControlPage />} />
        <Route path="/instance/:instanceId" element={<ControlPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <NotificationCenter />
    </>
  )
}
