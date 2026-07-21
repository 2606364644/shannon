import { RouterProvider } from "react-router-dom";
import { router } from "./router";
import { Toaster } from "@/components/ui/sonner";
import { BrandProvider } from "@/brand/BrandContext";

export default function App() {
  return (
    <BrandProvider>
      <RouterProvider router={router} />
      <Toaster />
    </BrandProvider>
  );
}
