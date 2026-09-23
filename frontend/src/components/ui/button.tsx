import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';
import { useMagneticHover } from '@/hooks/useMagneticHover';

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        // Gold on hover, not a dimmed blue: the primary action is the one place
        // the marketing pages' accent reaches an interactive control. The text
        // colour has to swap with it — near-white on honey-gold is ~1.3:1,
        // while --background on the same gold is ~10.9:1.
        default:
          'bg-primary text-primary-foreground shadow hover:bg-editorial hover:text-background',
        destructive:
          'bg-destructive text-destructive-foreground shadow-sm hover:bg-destructive/90',
        outline:
          'border border-border bg-background shadow-sm hover:bg-accent hover:text-accent-foreground',
        secondary:
          'bg-secondary text-secondary-foreground shadow-sm hover:bg-secondary/80',
        ghost: 'hover:bg-accent hover:text-accent-foreground',
        link: 'text-primary-bright underline-offset-4 hover:underline',
      },
      size: {
        default: 'h-9 px-4 py-2',
        sm: 'h-8 rounded-md px-3 text-xs',
        lg: 'h-10 rounded-md px-8',
        icon: 'h-9 w-9',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  (
    { className, variant, size, asChild = false, onMouseMove, onMouseLeave, onMouseDown, onMouseUp, ...props },
    forwardedRef,
  ) => {
    const Comp = asChild ? Slot : 'button';
    // Every button gets a small magnetic pull toward the cursor and a bouncy
    // snap back — one shared feel rather than reimplementing it per page.
    // Harmless on the size="icon" close buttons too: the effect is capped at
    // a few pixels (see useMagneticHover), not something that needs opting
    // out of case by case.
    const magnetic = useMagneticHover<HTMLButtonElement>();

    const setRefs = React.useCallback(
      (node: HTMLButtonElement | null) => {
        magnetic.ref.current = node;
        if (typeof forwardedRef === 'function') forwardedRef(node);
        else if (forwardedRef) (forwardedRef as React.MutableRefObject<HTMLButtonElement | null>).current = node;
      },
      [forwardedRef, magnetic.ref],
    );

    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={setRefs}
        onMouseMove={(event: React.MouseEvent<HTMLButtonElement>) => {
          magnetic.onMouseMove(event);
          onMouseMove?.(event);
        }}
        onMouseLeave={(event: React.MouseEvent<HTMLButtonElement>) => {
          magnetic.onMouseLeave();
          onMouseLeave?.(event);
        }}
        onMouseDown={(event: React.MouseEvent<HTMLButtonElement>) => {
          magnetic.onMouseDown();
          onMouseDown?.(event);
        }}
        onMouseUp={(event: React.MouseEvent<HTMLButtonElement>) => {
          magnetic.onMouseUp();
          onMouseUp?.(event);
        }}
        {...props}
      />
    );
  },
);
Button.displayName = 'Button';

export { Button, buttonVariants };
