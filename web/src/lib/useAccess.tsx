// Identity is rechecked against database grants; route checks are only UI.
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { supabase } from './supabase';
import { useSession } from './useSession';
type Access = { staff: boolean; admin: boolean; loading: boolean; userId: string | null };
const Context = createContext<Access>({staff:false,admin:false,loading:true,userId:null});
export function AccessProvider({children}: {children: ReactNode}) {
  const {session,loading} = useSession();
  const [access,setAccess] = useState<Access>({staff:false,admin:false,loading:true,userId:null});
  const uid=session?.user.id ?? null;
  useEffect(() => {
    let cancelled=false, sequence=0;
    setAccess({staff:false,admin:false,loading:!!uid,userId:uid});
    async function refresh() {
      if (!uid) return;
      const ticket=++sequence;
      try {
      const result=await supabase.rpc('dashboard_identity');
      let data=result.data;
      // Old deployment remains usable before migration 0016. Only the server's
      // existing is_staff check can authorize this compatibility path.
      if (result.error?.code === 'PGRST202' || result.error?.code === '42883') {
        const legacy=await supabase.rpc('is_staff');
        data={staff:legacy.data === true,admin:false};
      }
      if (!cancelled && ticket===sequence) setAccess({staff:data?.staff === true,admin:data?.admin === true,loading:false,userId:uid});
      } catch {
        if (!cancelled && ticket===sequence) setAccess({staff:false,admin:false,loading:false,userId:uid});
      }
    }
    void refresh();
    const interval=window.setInterval(() => void refresh(),30000);
    const focus=() => void refresh();
    window.addEventListener('focus',focus);
    return () => {cancelled=true;window.clearInterval(interval);window.removeEventListener('focus',focus);};
  },[uid]);
  const current = access.userId===uid ? access : {staff:false,admin:false,loading:true,userId:uid};
  return <Context.Provider value={{...current,loading:loading || current.loading}}>{children}</Context.Provider>;
}
export const useAccess=() => useContext(Context);
