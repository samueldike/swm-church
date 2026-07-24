# HTML to Web App Conversion Protocol

## Mission
Convert HTML templates into fully functional, production-ready web applications with complete functionality, proper routing, working interactions, and zero broken elements.

## Core Principles
1. **Zero Broken Links**: Every clickable element must lead somewhere meaningful
2. **Complete Functionality**: All interactive elements must work as intended
3. **Consistent Design**: Maintain the original template's design language throughout
4. **Production Ready**: Code should be clean, maintainable, and deployable

---

## Phase 1: Analysis & Understanding

### Step 1.1: Template Analysis
Before asking any questions, thoroughly analyze the provided HTML template(s):

**Extract & Document:**
- **Purpose**: What type of application is this? (e-commerce, dashboard, landing page, SaaS, portfolio, etc.)
- **Pages Identified**: List all HTML files and their apparent purposes
- **Navigation Structure**: Map out all navigation menus, links, and their targets
- **Interactive Elements**: Identify all buttons, forms, modals, tabs, dropdowns, etc.
- **Data Patterns**: Note any repeated data structures (product cards, blog posts, user profiles, etc.)
- **Third-party Integrations**: Identify any external libraries, APIs, or services referenced
- **Assets**: Catalog images, icons, fonts, and other resources
- **Design System**: Extract color schemes, typography, spacing patterns, component styles

**Create a Mental Model:**
```
Project Type: [e.g., E-commerce Dashboard]
Main Sections: [e.g., Products, Orders, Customers, Analytics]
Key Features: [e.g., Product CRUD, Order Management, Search, Filters]
Missing Components: [e.g., Product Detail Page, Checkout Flow, User Settings]
```

### Step 1.2: Present Your Understanding
Share your analysis with the user in a clear, structured format:

```
📋 TEMPLATE ANALYSIS COMPLETE

Project Type: [Your assessment]

Identified Pages:
- index.html → Homepage/Dashboard
- products.html → Product Listing
- [etc.]

Key Features Detected:
✓ User authentication UI
✓ Product catalog with filters
✓ Shopping cart interface
✓ [etc.]

Missing/Incomplete Elements:
⚠ Product detail pages (referenced but not included)
⚠ Checkout process
⚠ User profile/settings pages
⚠ [etc.]

Interactive Elements to Implement:
→ All navigation links
→ Search functionality
→ Filter/sort controls
→ Form submissions
→ Modal dialogs
→ [etc.]
```

---

## Phase 2: Technology Stack Selection

### Step 2.1: Recommend Stack Options
Based on your analysis, present 2-3 appropriate technology stacks:

**For Each Option, Specify:**
- Frontend Framework (React, Vue, Angular, Svelte, vanilla JS, etc.)
- Build Tool (Vite, Webpack, Parcel, etc.)
- Routing Solution (React Router, Vue Router, etc.)
- State Management (Context API, Redux, Zustand, Pinia, etc.)
- Styling Approach (Keep existing CSS, Tailwind, CSS Modules, Styled Components, etc.)
- Backend/API (if needed: Node/Express, Next.js API routes, Firebase, Supabase, etc.)
- Database (if needed: PostgreSQL, MongoDB, Firebase, local storage, etc.)

**Example Recommendations:**

```
STACK OPTIONS:

Option 1: React + Vite (Recommended for most projects)
├─ React 18 with functional components
├─ Vite for fast development
├─ React Router for navigation
├─ Context API or Zustand for state
├─ Existing CSS preserved + organized
└─ JSON Server or local state for data

Option 2: Next.js (For production/SEO needs)
├─ Next.js 14 with App Router
├─ Server components where beneficial
├─ Built-in routing and API routes
├─ Existing CSS converted to modules
└─ API routes + database integration

Option 3: Vue 3 + Vite (Alternative framework)
├─ Vue 3 Composition API
├─ Vite build tool
├─ Vue Router
├─ Pinia for state management
└─ Existing CSS preserved

Why I recommend Option 1:
[Brief explanation based on project needs]
```

### Step 2.2: Get User Confirmation
Ask the user to choose or suggest modifications:
```
Which stack would you prefer? Or would you like me to use a different approach?
```

---

## Phase 3: Conversion Planning

Once stack is confirmed, create a detailed implementation plan:

### Step 3.1: Project Structure
Define the complete file structure:

```
project-name/
├─ public/
│  ├─ images/
│  ├─ fonts/
│  └─ favicon.ico
├─ src/
│  ├─ components/
│  │  ├─ common/
│  │  │  ├─ Header.jsx
│  │  │  ├─ Footer.jsx
│  │  │  ├─ Button.jsx
│  │  │  └─ Modal.jsx
│  │  ├─ products/
│  │  │  ├─ ProductCard.jsx
│  │  │  ├─ ProductList.jsx
│  │  │  └─ ProductDetail.jsx
│  │  └─ [other feature folders]
│  ├─ pages/
│  │  ├─ Home.jsx
│  │  ├─ Products.jsx
│  │  ├─ ProductDetail.jsx
│  │  └─ [all other pages]
│  ├─ context/ (or store/)
│  │  ├─ AuthContext.jsx
│  │  └─ CartContext.jsx
│  ├─ hooks/
│  │  └─ useLocalStorage.js
│  ├─ utils/
│  │  └─ helpers.js
│  ├─ styles/
│  │  ├─ global.css
│  │  └─ [component styles]
│  ├─ data/
│  │  └─ mockData.js
│  ├─ App.jsx
│  └─ main.jsx
├─ package.json
└─ vite.config.js (or relevant config)
```

### Step 3.2: Component Breakdown
List every component to be created:

```
COMPONENT INVENTORY:

Layout Components:
□ Header (with navigation)
□ Footer
□ Sidebar (if applicable)
□ MainLayout wrapper

Common/Reusable Components:
□ Button (with variants)
□ Input/FormField
□ Card
□ Modal/Dialog
□ Dropdown
□ SearchBar
□ Pagination
□ LoadingSpinner
□ [etc.]

Feature Components:
□ ProductCard
□ ProductList
□ ProductFilters
□ ShoppingCart
□ CartItem
□ [etc.]

Page Components:
□ HomePage
□ ProductsPage
□ ProductDetailPage
□ CheckoutPage
□ ProfilePage
□ [etc.]
```

### Step 3.3: Functionality Checklist
Map every interactive element to its implementation:

```
FUNCTIONALITY IMPLEMENTATION:

Navigation:
□ All header links route correctly
□ Footer links work
□ Breadcrumbs (if present)
□ Back/forward navigation

Forms:
□ Search functionality with real filtering
□ Login/signup forms with validation
□ Product filters with live updates
□ Checkout forms with validation
□ Contact forms

Interactive UI:
□ Modals open/close properly
□ Dropdowns work
□ Tabs switch content
□ Accordions expand/collapse
□ Image galleries/carousels
□ Tooltips display

Data Operations:
□ Display product listings
□ Product detail views
□ Add to cart
□ Update cart quantities
□ Remove from cart
□ Search and filter
□ Sort functionality
□ Pagination

State Management:
□ Authentication state
□ Cart state
□ User preferences
□ Form state
□ Loading states
```

---

## Phase 4: Implementation

### Step 4.1: Setup & Configuration
Create the project foundation:

1. **Initialize Project**
   - Set up build tool configuration
   - Install all dependencies
   - Configure routing
   - Set up file structure

2. **Asset Migration**
   - Copy and organize all images
   - Extract and organize CSS
   - Set up fonts
   - Configure public assets

3. **Style Organization**
   - Extract global styles
   - Create CSS modules/component styles
   - Set up CSS variables for theming
   - Ensure responsive design is preserved

### Step 4.2: Component Development Priority

**Order of Implementation:**

1. **Foundation Layer**
   - Layout components (Header, Footer, MainLayout)
   - Basic routing structure
   - Global styles and theme

2. **Common Components**
   - Reusable UI components
   - Ensure all props and variants from original design
   - Test components in isolation

3. **Data Layer**
   - Create mock data structures
   - Set up context/state management
   - Create data fetching/manipulation utilities

4. **Feature Components**
   - Build feature-specific components
   - Integrate with data layer
   - Ensure all interactions work

5. **Pages**
   - Assemble components into pages
   - Implement page-specific logic
   - Connect routing

6. **Missing Pages**
   - Create all pages referenced but not provided
   - Follow the design language exactly
   - Ensure functional parity

### Step 4.3: Code Quality Standards

**Every Component Must:**
- Use proper component structure (functional components, hooks)
- Have clear, descriptive names
- Include PropTypes or TypeScript types
- Handle loading and error states
- Be responsive (maintain original breakpoints)
- Follow accessibility best practices
- Include comments for complex logic

**Example Component Template:**

```jsx
import React, { useState } from 'react';
import './ProductCard.css';

/**
 * ProductCard Component
 * Displays a single product with image, title, price, and add to cart
 * 
 * @param {Object} product - Product data object
 * @param {Function} onAddToCart - Callback when add to cart is clicked
 */
const ProductCard = ({ product, onAddToCart }) => {
  const [isHovered, setIsHovered] = useState(false);

  const handleAddToCart = () => {
    onAddToCart(product);
  };

  return (
    <div 
      className="product-card"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      <div className="product-image">
        <img src={product.image} alt={product.title} />
        {isHovered && (
          <button 
            className="quick-view-btn"
            onClick={() => {/* Quick view logic */}}
          >
            Quick View
          </button>
        )}
      </div>
      <div className="product-info">
        <h3>{product.title}</h3>
        <p className="product-price">${product.price}</p>
        <button 
          className="add-to-cart-btn"
          onClick={handleAddToCart}
        >
          Add to Cart
        </button>
      </div>
    </div>
  );
};

export default ProductCard;
```

### Step 4.4: Creating Missing Pages

When creating pages that were referenced but not provided:

1. **Analyze References**: Look at how the page is linked/described
2. **Follow Design Patterns**: Use the same design language, spacing, typography
3. **Logical Flow**: Ensure the page fits logically in the user journey
4. **Complete Functionality**: Implement all expected features

**Example: Product Detail Page (if missing)**

```jsx
// If the template shows product cards linking to /product/:id
// Create a fully functional ProductDetail page with:
// - Product images gallery
// - Title, description, price
// - Size/color variants (if applicable)
// - Quantity selector
// - Add to cart button
// - Related products section
// - Reviews section (if context suggests)
// All following the exact design system from the template
```

---

## Phase 5: Integration & Testing

### Step 5.1: Routing Integration
Ensure every link works:

```
ROUTING CHECKLIST:

□ All navigation menu items route correctly
□ Logo links to home
□ Footer links work
□ Button actions navigate or trigger functions
□ Dynamic routes work (e.g., /product/:id)
□ 404 page for invalid routes
□ Browser back/forward works correctly
```

### Step 5.2: Data Flow Testing
Verify all data operations:

```
DATA FLOW CHECKLIST:

□ Mock data loads correctly
□ Search filters data properly
□ Sorting works
□ Pagination updates correctly
□ Cart adds/removes items
□ Cart persists (localStorage/session)
□ Form submissions work
□ State updates trigger re-renders
```

### Step 5.3: UI/UX Testing
Check every interactive element:

```
INTERACTION CHECKLIST:

□ All buttons do something
□ All forms validate input
□ Modals open/close smoothly
□ Dropdowns expand/collapse
□ Hover states work
□ Active states work
□ Loading indicators show during async operations
□ Error messages display appropriately
□ Success messages confirm actions
```

### Step 5.4: Responsive Testing
Ensure mobile/tablet compatibility:

```
□ Layout adapts to mobile
□ Navigation becomes mobile menu
□ Images resize appropriately
□ Text remains readable
□ Buttons are touch-friendly
□ Forms work on mobile
```

---

## Phase 6: Documentation & Handoff

### Step 6.1: Create README.md
Provide complete documentation:

```markdown
# Project Name

## Overview
[Description of what the app does]

## Tech Stack
- Frontend: React 18
- Build Tool: Vite
- Routing: React Router v6
- State: Context API
- Styling: CSS Modules + Original CSS

## Features
- ✓ Product catalog with search and filters
- ✓ Shopping cart with persistence
- ✓ Responsive design
- ✓ [etc.]

## Getting Started

### Installation
```bash
npm install
```

### Development
```bash
npm run dev
```

### Build
```bash
npm run build
```

## Project Structure
[Explain folder organization]

## Key Components
[Brief description of main components]

## Data Management
[Explain how data is managed - mock data, API, etc.]

## Customization
[How to add products, change content, etc.]

## Future Enhancements
- [ ] Backend API integration
- [ ] User authentication
- [ ] Payment processing
- [ ] [etc.]
```

### Step 6.2: Code Comments
Ensure code is well-documented:

- Complex functions have explanatory comments
- Component purposes are clear
- State management logic is explained
- Any "magic numbers" are explained

### Step 6.3: Handoff Checklist

```
FINAL VERIFICATION:

Code Quality:
□ No console errors
□ No broken imports
□ All components render
□ Clean, consistent code style

Functionality:
□ All links work
□ All buttons do something
□ All forms validate
□ All interactive elements function

Completeness:
□ All original pages converted
□ All missing pages created
□ All assets included
□ Documentation complete

Production Ready:
□ Build runs successfully
□ No development artifacts
□ Environment variables documented
□ Deployment instructions included
```

---

## Quality Standards

### Non-Negotiables
1. **Zero broken links** - Every `<a>` tag must route somewhere
2. **Zero non-functional buttons** - Every button must trigger an action
3. **Complete pages** - If referenced, it must exist
4. **Consistent design** - New pages match original template exactly
5. **Working interactions** - Modals, dropdowns, forms all function
6. **Responsive** - Works on mobile, tablet, desktop
7. **Clean code** - Maintainable, commented, organized

### Success Criteria
A successful conversion means:
- A user can navigate the entire app without hitting dead ends
- All interactive elements respond appropriately
- The app looks identical to the original template
- The code is production-ready
- Another developer can understand and extend the code

---

## Common Pitfalls to Avoid

1. **Don't leave placeholder functions** - Implement or clearly mark as TODO
2. **Don't ignore broken links** - Every link must work
3. **Don't skip mobile testing** - Always check responsive design
4. **Don't hardcode data** - Use proper data structures
5. **Don't forget loading states** - Show feedback for async operations
6. **Don't ignore the details** - Hover effects, transitions matter
7. **Don't assume pages** - If it's not provided, create it properly

---

## AI Agent Guidelines

### Communication Style
- Be proactive in identifying issues
- Explain your reasoning for technical decisions
- Ask for clarification when template intentions are unclear
- Present options when multiple valid approaches exist

### When to Ask for Input
- Technology stack selection
- Missing page specifications (if context insufficient)
- Feature priority if scope is very large
- Design decisions for missing elements not inferable from template

### When to Proceed Autonomously
- Component structure (follow best practices)
- Code organization (follow conventions)
- Creating missing pages (if design patterns are clear)
- Implementing standard interactions

### Progress Updates
Provide regular updates in this format:

```
✅ Completed: Layout components, routing setup
🔄 In Progress: Product listing functionality
⏭️ Next: Shopping cart implementation
📋 Remaining: Checkout, User profile, Admin pages
```

---

## Example Workflow

1. **Receive HTML template(s)**
2. **Analyze** → Present findings
3. **Wait for stack selection** → User chooses
4. **Create implementation plan** → Share with user
5. **Begin coding** → Work through components systematically
6. **Test continuously** → Verify each piece works
7. **Create missing pages** → Following design system
8. **Final integration** → Connect everything
9. **Document** → README and code comments
10. **Deliver** → Fully functional, production-ready app

---

## Final Note

This is not about converting HTML syntax to JSX. This is about creating a **living, breathing, fully functional web application** that happens to use an HTML template as its design foundation. Every click should work. Every page should exist. Every feature should function. The end result should be production-ready code that a team could deploy and build upon.

**Zero broken pieces. Complete functionality. Production ready.**
