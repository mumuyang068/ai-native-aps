import {defineConfig} from 'vitest/config';
import react from '@vitejs/plugin-react';
export default defineConfig({base:'/workspace/',plugins:[react()],test:{environment:'node',include:['src/**/*.test.ts']}});
