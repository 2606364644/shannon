import { RouterProvider } from "react-router-dom";
import { router } from "./router";
import { Toaster } from "@/components/ui/sonner";
import { BrandProvider } from "@/brand/BrandContext";
import { AuthProvider } from "./auth/AuthContext";

export default function App() {
  return (
    <BrandProvider>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
      <Toaster />
    </BrandProvider>
  );
}
