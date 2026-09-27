import { clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

/** Merge class names, letting a later Tailwind class win over an earlier one. */
export function cn(...inputs) {
  return twMerge(clsx(inputs))
}
