"use client";

import { Menu } from "lucide-react";
import { useState } from "react";
import { Nav } from "@/components/shell/nav";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";

/** Navigation for narrow viewports, as a drawer that closes on selection. */
export function MobileNav() {
  const [open, setOpen] = useState(false);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="lg:hidden"
          aria-label="Open navigation"
        >
          <Menu className="size-5" />
        </Button>
      </SheetTrigger>
      <SheetContent side="left" className="w-72 border-line bg-surface p-0">
        <SheetHeader className="border-b border-line px-4 py-3.5">
          <SheetTitle className="text-left text-sm font-semibold text-ink">
            Hawkeye
          </SheetTitle>
        </SheetHeader>
        <div className="overflow-y-auto p-3">
          <Nav onNavigate={() => setOpen(false)} />
        </div>
      </SheetContent>
    </Sheet>
  );
}
