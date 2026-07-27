import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/** shadcn's class merge helper — conditional classes without specificity clashes. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
