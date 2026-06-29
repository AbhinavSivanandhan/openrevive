"use client";

import { useSyncExternalStore } from "react";

const COLLECTION_ID_STORAGE_KEY =
  "openrevive.demo.collection-id";

function subscribe(onStoreChange: () => void): () => void {
  function onStorage(event: StorageEvent): void {
    if (
      event.storageArea === window.localStorage &&
      (
        event.key === COLLECTION_ID_STORAGE_KEY ||
        event.key === null
      )
    ) {
      onStoreChange();
    }
  }

  window.addEventListener("storage", onStorage);

  return () => {
    window.removeEventListener("storage", onStorage);
  };
}

function getClientSnapshot(): string | null {
  return window.localStorage.getItem(
    COLLECTION_ID_STORAGE_KEY,
  );
}

function getServerSnapshot(): undefined {
  return undefined;
}

export function useOpenReviveCollectionId():
  | string
  | null
  | undefined {
  return useSyncExternalStore<
    string | null | undefined
  >(
    subscribe,
    getClientSnapshot,
    getServerSnapshot,
  );
}
