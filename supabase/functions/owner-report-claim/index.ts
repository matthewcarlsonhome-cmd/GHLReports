import {handle} from '../_shared/owner-workflow.ts';
Deno.serve(req=>handle(req,'claim',key=>Deno.env.get(key)));
