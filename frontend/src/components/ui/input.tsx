import { InputHTMLAttributes, forwardRef, useId } from 'react';

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ label, error, className = '', ...props }, ref) => {
    const generatedId = useId();
    const inputId = props.id || generatedId;
    const baseStyles =
      'w-full rounded-lg border bg-white/90 px-4 py-2.5 text-neutral-950 transition-all placeholder:text-neutral-400 focus:outline-none focus:ring-2 focus:ring-offset-0 disabled:cursor-not-allowed disabled:bg-neutral-100 disabled:text-neutral-400';

    const stateStyles = error
      ? 'border-red-600 focus:border-red-600 focus:ring-red-500'
      : 'border-neutral-950/20 focus:border-neutral-950 focus:ring-neutral-950/20';

    return (
      <div className="w-full">
        {label && (
          <label htmlFor={inputId} className="mb-2 block text-sm font-medium text-neutral-700">
            {label}
          </label>
        )}
        <input
          id={inputId}
          ref={ref}
          className={`${baseStyles} ${stateStyles} ${className}`}
          {...props}
        />
        {error && (
          <p className="mt-1 text-sm text-red-600">{error}</p>
        )}
      </div>
    );
  }
);

Input.displayName = 'Input';
