import { servePilotMedia } from "@/lib/pilot-media";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request, context: { params: Promise<{ parts?: string[] }> }) {
  const { parts = [] } = await context.params;
  return servePilotMedia(request, parts, process.env.HLS_PILOT_ROOT);
}