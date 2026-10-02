import {handle} from '../_shared/owner-workflow.ts';
Deno.serve(req=>handle(req,'result',key=>Deno.env.get(key)));
