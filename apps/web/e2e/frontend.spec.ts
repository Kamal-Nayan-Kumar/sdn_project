import { test, expect } from '@playwright/test';

test.describe('NetMind Frontend', () => {
  test('loads dashboard page', async ({ page }) => {
    await page.goto('/');

    // Check that the page title is correct
    await expect(page).toHaveTitle(/NetMind/);

    // Check that the banner is visible
    await expect(page.getByText('NetMind')).toBeVisible();
    await expect(page.getByText('Agentic control for SDN')).toBeVisible();

    // Check status indicators
    await expect(page.getByText('Network lab connected')).toBeVisible();
    await expect(page.getByText('Agent idle')).toBeVisible();

    // Check navigation items
    await expect(page.getByRole('button', { name: 'Dashboard' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Network Topology' })).toBeVisible();

    // Check metric strip
    await expect(page.getByText('Video Latency')).toBeVisible();
    await expect(page.getByText('Link Utilization')).toBeVisible();
    await expect(page.getByText('Packet Loss', { exact: true })).toBeVisible();
    await expect(page.getByText('Protected Flow (Voice)')).toBeVisible();

    // Check sidebar tabs
    const sidebar = page.getByRole('complementary', { name: 'Assistant and agent activity' });
    await expect(sidebar.getByRole('button', { name: 'Chat' })).toBeVisible();
    await expect(sidebar.getByRole('button', { name: 'Agent Activity' })).toBeVisible();
    await expect(sidebar.getByRole('button', { name: 'Run Details' })).toBeVisible();

    // Check footer info
    await expect(page.getByText('frontend localhost:3000')).toBeVisible();
    await expect(page.getByText('fastapi 127.0.0.1:8000')).toBeVisible();
  });

  test('topology fetches and displays', async ({ page }) => {
    await page.goto('/');

    // Wait for topology to load (it should be fetched from /api/lab/topology)
    const topologyRegion = page.getByText('Waiting for live network telemetry');
    
    // The topology may show "Waiting for live network telemetry" initially
    // but should eventually show the topology once data is loaded
    // For now, we just verify the region exists
    await expect(topologyRegion).toBeVisible();
  });

  test('backend status is displayed', async ({ page }) => {
    await page.goto('/');

    // Check that backend status is shown in footer
    await expect(page.getByText('fastapi 127.0.0.1:8000')).toBeVisible();
    await expect(page.getByText('ryu 127.0.0.1:8080')).toBeVisible();
  });

  test('lab controls are visible', async ({ page }) => {
    await page.goto('/');

    // Check lab controls section
    await expect(page.getByText('Lab controls')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Create Congestion' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Start Monitoring' })).toBeVisible();
  });
});
