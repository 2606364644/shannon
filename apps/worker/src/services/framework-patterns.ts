// Copyright (C) 2025 Keygraph, Inc.
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License version 3
// as published by the Free Software Foundation.

/**
 * Framework detection patterns
 *
 * Defines patterns for auto-generated REST frameworks that commonly
 * create authorization and XSS vulnerabilities due to lack of ownership
 * validation on CRUD endpoints.
 */

export interface FrameworkPattern {
  readonly name: string;
  readonly detectionPatterns: {
    readonly import?: readonly string[];
    readonly initialize?: readonly string[];
    readonly config?: readonly string[];
  };
  readonly endpointTemplates: readonly EndpointTemplate[];
  readonly vulnerabilityPatterns: readonly string[];
}

export interface EndpointTemplate {
  readonly methods: readonly string[];
  readonly pathTemplate: string;
  readonly defaultMiddleware: readonly string[];
  readonly notes: string;
}

export const FRAMEWORK_PATTERNS: readonly FrameworkPattern[] = [
  {
    name: 'finale-rest',
    detectionPatterns: {
      import: ['require("express-finale")', 'require("finale-rest")', 'import.*finale.*from'],
      initialize: ['finale.initialize(', 'finale.resource('],
      config: ['finale.resource('],
    },
    endpointTemplates: [
      {
        methods: ['GET', 'POST', 'PUT', 'DELETE'],
        pathTemplate: '/api/{Model}s',
        defaultMiddleware: ['isAuthenticated'],
        notes: 'Auto-generated CRUD operations, no ownership validation by default',
      },
      {
        methods: ['GET', 'POST', 'PUT', 'DELETE'],
        pathTemplate: '/api/{Model}s/:id',
        defaultMiddleware: ['isAuthenticated'],
        notes: 'Individual resource operations, commonly vulnerable to IDOR',
      },
    ],
    vulnerabilityPatterns: [
      'No ownership check on finale resource operations',
      'DELETE endpoint often unblocked by default',
      'PUT endpoint may lack role checks',
    ],
  },
  {
    name: 'epilogue',
    detectionPatterns: {
      import: ['require("epilogue")', 'import.*epilogue.*from'],
      initialize: ['epilogue.initialize(', 'epilogue.resource('],
      config: ['epilogue.resource('],
    },
    endpointTemplates: [
      {
        methods: ['GET', 'POST', 'PUT', 'DELETE'],
        pathTemplate: '/api/{resource}',
        defaultMiddleware: [],
        notes: 'Similar to finale, auto-generated CRUD',
      },
    ],
    vulnerabilityPatterns: [
      'Epilogue resources lack ownership validation by default',
      'Mass operations enabled without explicit disable',
    ],
  },
] as const;
