import React from 'react';
import { renderToString } from 'react-dom/server';
import { App } from './main';

export function render() {
  return renderToString(<React.StrictMode><App /></React.StrictMode>);
}
