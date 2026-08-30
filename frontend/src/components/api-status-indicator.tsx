"use client";

import { useState, useEffect } from "react";
import { getApiStatus } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Wifi, WifiOff } from "lucide-react";

export function ApiStatusIndicator() {
  const [isOnline, setIsOnline] = useState<boolean | null>(null);

  useEffect(() => {
    getApiStatus().then(setIsOnline);
  }, []);

  if (isOnline === null) return null;

  if (isOnline) return null; // API 正常时不显示

  return (
    <Badge variant="secondary" className="text-xs gap-1 bg-amber-50 text-amber-600 border border-amber-200">
      <WifiOff className="h-3 w-3" />
      演示模式
    </Badge>
  );
}
