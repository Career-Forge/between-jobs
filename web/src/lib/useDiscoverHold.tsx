import { createContext, useContext, useState, type ReactNode } from "react";
import { createDiscoverHold, type DiscoverHold } from "./discoverHold";

// Hands the signed-in person's Discover hold (lib/discoverHold.ts) to the page. The store has to outlive the
// page, which unmounts whenever the person goes elsewhere, so it is made by a provider that stays mounted
// around the routes (App.tsx) and is created once per mount of that provider.
//
// App keys the provider by the person's id: a different person gets a different store, and the one before is
// dropped with its provider. It is drawn only for a signed-in person, so signing out drops it too.

const DiscoverHoldContext = createContext<DiscoverHold | null>(null);

export function DiscoverHoldProvider({ children }: { children: ReactNode }) {
  const [hold] = useState(() => createDiscoverHold());
  return <DiscoverHoldContext.Provider value={hold}>{children}</DiscoverHoldContext.Provider>;
}

// The hold for this page. With no provider above it (the page drawn on its own, as its tests do) the page
// gets a store private to that mount, which starts empty and is gone with it.
export function useDiscoverHold(): DiscoverHold {
  const provided = useContext(DiscoverHoldContext);
  const [own] = useState(() => createDiscoverHold());
  return provided ?? own;
}
