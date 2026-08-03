"use client";

import { useState, useEffect, useRef } from "react";
import { useToast } from "@/components/ui/toast";

export function NetworkStatus() {
  const [online, setOnline] = useState(true);
  const { info } = useToast();
  const wasOffline = useRef(false);

  useEffect(() => {
    const handleOnline = () => {
      setOnline(true);
      if (wasOffline.current) {
        info("网络已恢复");
      }
      wasOffline.current = false;
    };
    const handleOffline = () => {
      setOnline(false);
      wasOffline.current = true;
    };

    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);

    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
  }, [info]);

  if (online) return null;

  return (
    <div className="fixed top-0 left-0 right-0 z-50 bg-red-600 text-white text-center py-2 text-sm font-medium">
      网络连接已断开，请检查网络
    </div>
  );
}
